/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Up to four beams of light that sweep to and fro from one point, like searchlights in haze.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: 4 beams, one atan, one square root, 5 sines and 2 divisions a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Sweeps a second",
            "TYPE": "float",
            "MIN": 0.05,
            "MAX": 1.5,
            "DEFAULT": 0.4
        },
        {
            "NAME": "beams",
            "LABEL": "Beams",
            "TYPE": "long",
            "VALUES": [
                1,
                2,
                3,
                4
            ],
            "LABELS": [
                "1",
                "2",
                "3",
                "4"
            ],
            "DEFAULT": 3
        },
        {
            "NAME": "width",
            "LABEL": "Beam width",
            "TYPE": "float",
            "MIN": 0.03,
            "MAX": 0.4,
            "DEFAULT": 0.12
        },
        {
            "NAME": "sweep",
            "LABEL": "Sweep",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 1.5,
            "DEFAULT": 0.7
        },
        {
            "NAME": "haze",
            "LABEL": "Haze",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.35,
                0.65,
                1.0,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.4,
                0.75,
                1.0
            ]
        },
        {
            "NAME": "origin",
            "LABEL": "Where the beams start",
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
                0.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 at = clamp(origin, 0.0, 1.0);
    vec2 p = isf_FragNormCoord - at;
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    p.x *= wide;
    float r = length(p);
    float a = atan(p.y, p.x + 0.00001);
    // the beams fan out towards the middle of the picture, wherever they start
    vec2 aim = vec2(0.5) - at;
    float middle = abs(aim.x) * wide > abs(aim.y) ? (aim.x > 0.0 ? 0.0 : 3.14159) : (aim.y >= 0.0 ? 1.5708 : -1.5708);
    float count = float(beams);
    vec3 glow = vec3(0.0);
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        if (fi < count) {
            // each beam's own to and fro, folded to 0..1 before it becomes an angle so it stays exact for hours
            float swing = sin(6.2831853 * fract(TIME * rate * (1.0 - 0.13 * fi) + 0.29 * fi));
            float aimed = middle + (fi - 0.5 * (count - 1.0)) * 0.45 + sweep * swing;
            float off = abs(mod(a - aimed + 3.14159, 6.2831853) - 3.14159);
            float core = 1.0 - smoothstep(0.0, width, off);
            float soft = 1.0 - smoothstep(0.0, width * 3.0, off);
            vec3 tone = mod(fi, 2.0) < 0.5 ? first.rgb : second.rgb;
            glow += tone * (core * core * 0.9 + soft * 0.22 * (0.4 + haze));
        }
    }
    // light thins out with distance; the haze near the lamps is always lit, so the picture is never empty
    float reach = 1.0 / (1.0 + 0.9 * r);
    vec3 col = glow * reach * smoothstep(0.0, 0.05, r);
    vec3 both = mix(first.rgb, second.rgb, 0.5 + 0.35 * sin(a * 2.0));
    col += both * (0.10 + 0.30 * haze) / (1.0 + 5.0 * r * r) + vec3(0.02, 0.02, 0.04);
    col = col / (1.0 + 0.35 * col);
    gl_FragColor = vec4(col * 1.25, 1.0);
}
