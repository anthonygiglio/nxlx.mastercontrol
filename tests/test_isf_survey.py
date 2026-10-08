# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""tools/isf-survey.py: a folder of ISF files sorted by what each one needs from the engine."""
import importlib.util
import io
import json
import os
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
spec = importlib.util.spec_from_file_location("isf_survey", os.path.join(REPO, "tools", "isf-survey.py"))
survey = importlib.util.module_from_spec(spec)
spec.loader.exec_module(survey)

MAIN = "\nvoid main() { gl_FragColor = vec4(isf_FragNormCoord, 0.5, 1.0); }\n"
READS = "\nvoid main() { gl_FragColor = IMG_THIS_PIXEL(inputImage); }\n"


def isf(head, body=MAIN):
    return "/*" + json.dumps(head) + "*/" + body


FILES = {
    "plain.fs": isf({"INPUTS": [{"NAME": "speed", "TYPE": "float"}]}),
    "filter.fs": isf({"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}]}, READS),
    "wipe.fs": isf({"INPUTS": [{"NAME": "startImage", "TYPE": "image"}, {"NAME": "endImage", "TYPE": "image"},
                               {"NAME": "progress", "TYPE": "float"}]}, READS.replace("inputImage", "startImage")),
    "trail.fs": isf({"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}],
                     "PASSES": [{"TARGET": "last", "PERSISTENT": True}, {}]}, READS),
    "two.fs": isf({"PASSES": [{"TARGET": "small", "WIDTH": "$WIDTH/4"}, {}]}),
    "sound.fs": isf({"INPUTS": [{"NAME": "fft", "TYPE": "audioFFT"}]}),
    "logo.fs": isf({"IMPORTED": {"logo": {"PATH": "logo.png"}}}),
    "edges.fs": isf({"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}]}, "\nvarying vec2 left_coord;\n" + READS),
    "edges.vs": "void main() { isf_vertShaderInit(); }\n",
    "own.fs": isf({"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}]}, "\nuniform sampler2D secret;\n" + READS),
    "many.fs": isf({"INPUTS": [{"NAME": "a%d" % i, "TYPE": "float"} for i in range(30)]}),
    "twice.fs": '/*{"DESCRIPTION": "a", "DESCRIPTION": "b"}*/' + MAIN,
    "heavy.fs": isf({}, "\nvoid main() {\n    float a = 0.0;\n    for (int i = 0; i < 64; i++) { a += sin(float(i)); }\n"
                        "    float big = 100000.0;\n    gl_FragColor = vec4(a, big, 0.0, 1.0);\n}\n"),
    "notes.txt": "not a shader",
}


class SurveyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, text in FILES.items():
            with open(os.path.join(self.tmp.name, name), "w") as f:
                f.write(text)
        self.rows = {r["file"]: r for r in survey.survey(self.tmp.name)}

    def test_each_file_is_sorted_by_what_it_needs(self):
        self.assertEqual(sorted(self.rows), sorted(n for n in FILES if n.endswith(".fs")))
        needs = {n: r["needs"] for n, r in self.rows.items()}
        self.assertEqual(needs, {
            "plain.fs": [], "heavy.fs": [], "filter.fs": ["filter"], "wipe.fs": ["transition"],
            "trail.fs": ["filter", "passes", "persistent"], "two.fs": ["passes"], "sound.fs": ["audio"], "logo.fs": ["imported"],
            "edges.fs": ["filter", "vertex"], "own.fs": ["checks", "filter"], "many.fs": ["limits"], "twice.fs": ["checks"]})
        self.assertEqual(sorted(n for n, r in self.rows.items() if r["translates"]), ["heavy.fs", "plain.fs"])
        self.assertIn("uniform", self.rows["own.fs"]["checks"])
        self.assertIn("twice", self.rows["twice.fs"]["checks"])
        for r in self.rows.values():                                # a refused file always says why and what it needs
            self.assertEqual(bool(r["refused"]), not r["translates"])
            self.assertTrue(r["translates"] or r["needs"], r)

    def test_cost_is_a_crude_count_with_warnings_for_opengl_es(self):
        self.assertEqual(self.rows["plain.fs"]["cost"]["level"], "low")
        heavy = self.rows["heavy.fs"]["cost"]
        self.assertEqual((heavy["level"], heavy["loops"], heavy["largest_bound"]), ("high", 1, 64))
        self.assertTrue(any("65504" in n for n in heavy["notes"]), heavy)
        self.assertEqual(self.rows["filter.fs"]["cost"]["picture_reads"], 1)

    def test_what_each_feature_unlocks_alone_and_with_others(self):
        u = survey.unlocks(list(self.rows.values()))
        self.assertEqual(u["filter"]["alone"], ["filter.fs"])
        self.assertEqual(sorted(u["filter"]["with_others"]), ["edges.fs", "own.fs", "trail.fs"])
        self.assertEqual((u["transition"]["alone"], u["passes"]["alone"], u["persistent"]["alone"]), (["wipe.fs"], ["two.fs"], []))
        out = io.StringIO()
        survey.report(list(self.rows.values()), out)
        text = out.getvalue()
        self.assertIn("12 .fs files, 2 translate today, 10 are refused", text)
        self.assertIn("filter      alone:   1   together with other features:   3", text)

    def test_a_credit_comment_before_the_header_is_not_taken_for_the_header(self):
        """The survey reads the header where the translator finds it: after the comments at the top of the file."""
        lead = "// Flat Probe, by Ana Example (a made-up credit)\n/* [not] the {header} */\n\n"
        files = {"credited.fs": lead + isf({"CREDIT": "Ana Example", "INPUTS": [{"NAME": "time", "TYPE": "float"}]}),
                 "credited-filter.fs": lead + isf({"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}]}, READS),
                 "credited-sound.fs": (lead + isf({"INPUTS": [{"NAME": "fft", "TYPE": "audioFFT"}]})).replace("\n", "\r\n"),
                 "credited-twice.fs": lead + '/*{"CREDIT": "a", "CREDIT": "b"}*/' + MAIN,
                 "no-header.fs": "/* only a credit */" + MAIN}
        with tempfile.TemporaryDirectory() as folder:
            for name, text in files.items():
                with open(os.path.join(folder, name), "w", newline="") as f:
                    f.write(text)
            rows = {r["file"]: r for r in survey.survey(folder)}
        self.assertEqual({n: r["needs"] for n, r in rows.items()}, {
            "credited.fs": [], "credited-filter.fs": ["filter"], "credited-sound.fs": ["audio"], "credited-twice.fs": ["checks"],
            "no-header.fs": ["checks"]})
        self.assertEqual((rows["credited.fs"]["translates"], rows["credited.fs"]["credit"]), (True, "Ana Example"))
        self.assertTrue(rows["credited-filter.fs"]["as_effect"])
        self.assertIn("twice", rows["credited-twice.fs"]["checks"])
        self.assertIn("not an ISF file", rows["no-header.fs"]["refused"])

    def test_the_bundled_pack_translates_whole(self):
        rows = survey.survey(os.path.join(REPO, "pvj", "shaders.d", "isf-files"))
        self.assertEqual(len(rows), 7)
        self.assertEqual([r["file"] for r in rows if not r["translates"]], [])


if __name__ == "__main__":
    unittest.main()
