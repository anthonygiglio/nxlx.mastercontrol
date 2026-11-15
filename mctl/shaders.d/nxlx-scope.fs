/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Up to three glowing traces like an oscilloscope, kicked by a beat (at most 3 a second).",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "medium: 3 traces, 9 sines, 3 square roots and 6 divisions a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Beats a second",
            "TYPE": "float",
            "MIN": 0.25,
            "MAX": 3.0,
            "DEFAULT": 1.5
        },
        {
            "NAME": "waves",
            "LABEL": "Waves across",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 12.0,
            "DEFAULT": 4.0
        },
        {
            "NAME": "gain",
            "LABEL": "Height",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "punch",
            "LABEL": "Kick on the beat",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "traces",
            "LABEL": "Traces",
            "TYPE": "long",
            "VALUES": [
                1,
                2,
                3
            ],
            "LABELS": [
                "1",
                "2",
                "3"
            ],
            "DEFAULT": 3
        },
        {
            "NAME": "shape",
            "LABEL": "Wave",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Beating sines",
                "Soft square",
                "Packet"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "trace",
            "LABEL": "Trace colour",
            "TYPE": "color",
            "DEFAULT": [
                0.35,
                1.0,
                0.6,
                1.0
            ]
        },
        {
            "NAME": "screen",
            "LABEL": "Screen colour",
            "TYPE": "color",
            "DEFAULT": [
                0.03,
                0.14,
                0.14,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord;
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    float x = (uv.x - 0.5) * wide;
    float y = uv.y - 0.5;
    // the beat: capped at 3 a second, whatever the number says; each beat kicks the height and then lets go
    float hz = min(rate, 3.0);
    float beats = TIME * hz;
    float since = fract(beats);
    float kick = 1.0 - punch + punch * (0.25 + 0.75 * (1.0 - since) * (1.0 - since));
    // the screen: a dim ruled grid, brighter in the middle
    vec2 cell = abs(fract(vec2(x, y) * 8.0) - 0.5);
    float rule = 1.0 - smoothstep(0.0, 0.03, min(cell.x, cell.y));
    vec3 col = screen.rgb * (0.75 + 0.5 * rule) * (1.25 - 1.3 * dot(vec2(x / wide, y), vec2(x / wide, y)));
    float count = float(traces);
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        if (fi < count) {
            // the trace runs along: whole waves a second, folded to 0..1 so the angle stays exact for hours
            float run = 6.2831853 * fract(TIME * hz * (0.5 + 0.25 * fi) + 0.3 * fi);
            float k = waves * (1.0 + 0.5 * fi) * 6.2831853 / wide;
            float up = sin(x * k - run);
            float lean = cos(x * k - run);
            // the slow shape over the fast wave: a beat between two sines, or a packet that travels to and fro
            float slowly = sin(x * k * 0.13 + run * 0.5 + fi);
            if (shape == 2) {
                float from = x + 0.4 * wide * slowly;
                slowly = 1.0 / (1.0 + 9.0 * from * from);
            }
            float wave = up * slowly;
            float slope = lean * slowly;
            if (shape == 1) {
                wave = clamp(2.5 * up, -1.0, 1.0);
                slope = abs(up) < 0.4 ? 2.5 * lean : 0.0;
            }
            float tall = gain * kick * 0.36 * (1.0 - 0.22 * fi);
            float at = tall * wave + (fi - 0.5 * (count - 1.0)) * 0.12 * (1.0 - gain);
            // the distance to the trace, not just the height above it: where the trace is steep it would turn into
            // dots otherwise
            float steep = tall * k * slope;
            float off = abs(y - at) / sqrt(1.0 + steep * steep);
            float beam = 0.006 / (off + 0.006);
            vec3 tone = mix(trace.rgb, trace.gbr, 0.35 * fi);
            col += tone * (beam * beam * 0.9 + 0.10 * beam) * (1.0 - 0.2 * fi);
        }
    }
    gl_FragColor = vec4(col / (1.0 + 0.25 * col), 1.0);
}
