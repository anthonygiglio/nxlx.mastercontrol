#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""How much of a folder of ISF files this box can run: every .fs file is put through the project's own translator
(pvj/shaders.py) and sorted into a bucket, with the reason when it is refused.

    python3 tools/isf-survey.py /path/to/ISF-Files/ISF            a report in plain text
    python3 tools/isf-survey.py /path/to/ISF-Files/ISF --json     the same as JSON

A developer's tool: it is not installed on a box and reads files only. "Translates" means the translator took the
file, which is not the same as drawing: only a real player with a GPU can say that (tests/test_shaders_gpu.py in CI).

"Translates" is said of a generator: a shader that draws from nothing and takes the screen. A file that is refused
as a generator is tried once more as an effect, a filter of the playing picture (pvj/effects.py), and the report
says how many of those the box takes that way, each with the work counted from its text.

What a file needs is read from its JSON header and its code independently of the translator (which stops at the
first reason), so a file that needs two things is counted under both, and "what would unlock it" is exact:
  filter      one picture to work on (an `image` input, usually inputImage)
  transition  two pictures and a progress value (startImage, endImage)
  passes      more than one pass, or a pass with a TARGET or its own size
  persistent  a picture kept between frames (PERSISTENT, PERSISTENT_BUFFERS)
  audio       sound, as a waveform or FFT picture
  imported    pictures loaded from other files (IMPORTED)
  vertex      a vertex shader of its own (a .vs file beside it)
  limits      larger than the upload limits (file size, header size, number of inputs)
  checks      the code or header trips one of the translator's safety checks even with all of the above in place
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pvj import effects as E, shaders as S  # noqa: E402

FEATURES = ("filter", "transition", "passes", "persistent", "audio", "imported", "vertex", "limits", "checks")
_CALLS = re.compile(r"\b(sin|cos|tan|atan|asin|acos|pow|exp|log|sqrt|length|distance|normalize|mod|fract|smoothstep|mix)\s*\(")
_FOR = re.compile(r"\bfor\s*\(([^;]*);([^;]*);")
_IMG_CALL = re.compile(r"\bIMG_(?:NORM_PIXEL|PIXEL|THIS_NORM_PIXEL|THIS_PIXEL)\b")
_BIG = re.compile(r"(?<![\w.])(\d{5,}(?:\.\d*)?)(?![\w.])")


def header_start(text):
    """Where the header comment starts: where the translator finds it (after comments and blank space at the top, so
    a credit comment is not taken for the header), else the first comment of any kind, else -1."""
    try:
        return S.find_header(text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")), True
    except S.ShaderError:
        return text.find("/*"), False


def header(text):
    """The JSON header read leniently (the survey must describe files the translator would refuse)."""
    a, found = header_start(text)
    if found:
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    b = text.find("*/", a + 2)
    if a < 0 or b < 0:
        return {}, text
    try:
        head = json.loads(text[a + 2:b], strict=False)
    except ValueError:
        head = {}
    return (head if isinstance(head, dict) else {}), text[b + 2:]


def header_problem(text):
    """What the translator's strict reading of the JSON header refuses (a key twice, NaN, text it cannot read)."""
    a, found = header_start(text)
    if found:
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    b = text.find("*/", a + 2)
    if a < 0 or b < 0:
        return "not an ISF file: no JSON header comment"
    if b - a > S.MAX_HEADER:
        return ""                           # counted under limits
    try:
        json.loads(text[a + 2:b], parse_constant=S._no_constant, object_pairs_hook=S._no_repeats)
    except S.ShaderError as e:
        return str(e)
    except (ValueError, RecursionError):
        return "the JSON header at the top cannot be read"
    return ""


def needs(head, body, path, size):
    """The engine features a file needs, from its header and code."""
    out = set()
    inputs = [i for i in head.get("INPUTS") or [] if isinstance(i, dict)]
    images = [i.get("NAME") for i in inputs if i.get("TYPE") in ("image", "cube")]
    if "startImage" in images or "endImage" in images:
        out.add("transition")
    elif images:
        out.add("filter")
    if any(i.get("TYPE") in ("audio", "audioFFT") for i in inputs):
        out.add("audio")
    passes = [p for p in head.get("PASSES") or [] if isinstance(p, dict)]
    if head.get("PERSISTENT_BUFFERS") or any(p.get("PERSISTENT") for p in passes):
        out.add("persistent")
    if len(passes) > 1 or any(p.get("TARGET") or p.get("WIDTH") or p.get("HEIGHT") for p in passes):
        out.add("passes")
    if head.get("IMPORTED"):
        out.add("imported")
    if os.path.exists(path[:-3] + ".vs"):
        out.add("vertex")
    if size > S.MAX_SOURCE or len(inputs) > S.MAX_INPUTS or size - len(body.encode("utf-8", "replace")) > S.MAX_HEADER:
        out.add("limits")
    return out


def checks(head, body):
    """What the translator's safety checks would still say about the code if every missing feature existed: the
    file is tried again as a plain generator, with its picture and sound inputs taken out and the IMG_ calls and the
    varyings of a vertex shader put aside. "" when nothing is left."""
    inputs = [i for i in head.get("INPUTS") or [] if isinstance(i, dict) and i.get("TYPE") in S.TYPES][:S.MAX_INPUTS]
    code = re.sub(r"\bIMG_(?:NORM_PIXEL|PIXEL|THIS_NORM_PIXEL|THIS_PIXEL|SIZE)\s*\(", "surveyImg(", S_strip(body))
    code = re.sub(r"(?m)^[ \t]*(?:varying|in)\b[^;(]*;", "", code)      # what its .vs hands over: the vertex feature
    try:
        S.translate(S.parse("/*" + json.dumps({"INPUTS": inputs}) + "*/\n" + code), (1280, 720))
    except S.ShaderError as e:
        return str(e)
    return ""


def S_strip(body):
    try:
        return S.strip_comments(body)
    except S.ShaderError:
        return body


def cost(body):
    """A crude count of work per pixel, read off the text: loops and their written bounds, picture reads, calls of
    the costlier built-in functions. It knows nothing of branches or of what a loop holds; a guide for a first sort."""
    code = S_strip(body)
    loops, bound, open_ended = 0, 1, False
    for init, cond in _FOR.findall(code):
        loops += 1
        m = re.fullmatch(r"\s*\w+\s*[<>]=?\s*(\d+)(?:\.\d*)?\s*", cond)
        if m:
            bound = max(bound, int(m.group(1)))
        else:
            open_ended = True
    whiles = len(re.findall(r"\bwhile\s*\(", code))
    reads = len(_IMG_CALL.findall(code))
    calls = len(_CALLS.findall(code))
    notes = []
    if whiles or open_ended:
        notes.append("a loop whose end is not a written number: refused by OpenGL ES 2, and its cost is unknown")
    if re.search(r"\b(?:uint|uvec[234])\b|>>|<<|(?<![&|])[&|^](?![&|=])", code):
        notes.append("whole-number bit work (uint, shifts): needs OpenGL ES 3, not 2")
    if re.search(r"\b(?:float|int|vec[234]|bool)\s+\w+\s*\[\s*\w*\s*\]\s*=|\[\]\s*\(", code):
        notes.append("arrays with initial values: need OpenGL ES 3")
    big = [float(x) for x in _BIG.findall(code) if float(x) > 65504.0]
    if big:
        notes.append("a number above 65504 (%g): black on OpenGL ES where precision is medium" % max(big))
    if re.search(r"\b(?:dFdx|dFdy|fwidth)\s*\(", code):
        notes.append("derivatives (dFdx, fwidth): an extension on OpenGL ES 2")
    if re.search(r"=\s*\d+\s*;", re.sub(r"\b(?:const\s+)?(?:int|ivec\d|uint)\b[^;]*;", "", code)) and re.search(r"\bfloat\s+\w+\s*=\s*\d+\s*;", code):
        notes.append("a float set from a whole number (float x = 1;): an error on OpenGL ES")
    level = "low"
    if whiles or open_ended or bound > 32:
        level = "high"
    elif bound > 8 or calls > 40 or reads > 9:
        level = "medium"
    return {"level": level, "loops": loops, "largest_bound": bound, "whiles": whiles, "picture_reads": reads, "calls": calls,
            "notes": notes}


def survey(folder):
    rows = []
    for name in sorted(os.listdir(folder), key=str.lower):
        if not name.endswith(".fs"):
            continue
        path = os.path.join(folder, name)
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", "replace")
        head, body = header(text)
        need = needs(head, body, path, len(data))
        try:
            S.translate(S.parse(data), (1280, 720))
            verdict = ""
        except S.ShaderError as e:
            verdict = str(e)
        effect, work = "", None
        if verdict:                         # not a generator: is it a filter the box can put over a picture?
            try:
                parsed = S.parse(data, S.FILTER)
                E.translate(parsed)
                work = E.estimate(parsed)
                if os.path.exists(path[:-3] + ".vs"):
                    effect = "it has a vertex shader of its own (.vs), which is not read"
            except S.ShaderError as e:
                effect = str(e)
        left = header_problem(text) or checks(head, body)
        if verdict and not left and not need - {"vertex"}:
            left = verdict                  # nothing else explains the refusal: it is a check
        if left and verdict:
            need.add("checks")
        rows.append({"file": name, "bytes": len(data), "credit": S._text(head.get("CREDIT")),
                     "categories": [c for c in head.get("CATEGORIES") or [] if isinstance(c, str)],
                     "translates": not verdict, "refused": verdict, "needs": sorted(need), "checks": left if "checks" in need else "",
                     "cost": cost(body), "as_effect": bool(verdict) and not effect, "effect_refused": effect,
                     "effect_work": work if (verdict and not effect) else None})
    return rows


def unlocks(rows):
    """Per feature: how many refused files it unlocks alone, and how many need it together with others."""
    out = {}
    refused = [r for r in rows if not r["translates"]]
    for f in FEATURES:
        alone = [r["file"] for r in refused if r["needs"] == [f]]
        also = [r["file"] for r in refused if f in r["needs"] and r["needs"] != [f]]
        out[f] = {"alone": alone, "with_others": also}
    return out


def report(rows, out=sys.stdout):
    w = out.write
    ok = [r for r in rows if r["translates"]]
    w("%d .fs files, %d translate today, %d are refused\n\n" % (len(rows), len(ok), len(rows) - len(ok)))
    w("TRANSLATES TODAY (cost is a crude count from the text, not a measurement)\n")
    for r in ok:
        c = r["cost"]
        w("  %-30s %-6s loops %d (largest %d), calls %d%s%s\n" % (
            r["file"], c["level"], c["loops"], c["largest_bound"], c["calls"],
            "; has a .vs file that is not read" if "vertex" in r["needs"] else "",
            "".join("; " + n for n in c["notes"])))
    fx = [r for r in rows if r.get("as_effect")]
    w("\nTRANSLATES AS AN EFFECT, A FILTER OVER THE PLAYING PICTURE: %d files (work is a count from the text: reads of the picture and loop rounds for one pixel)\n" % len(fx))
    for r in fx:
        e = r["effect_work"]
        w("  %-34s %-6s reads %d, rounds %d%s\n" % (r["file"], e["weight"], e["reads"], e["rounds"], "" if e["sure"] else "; NOT COUNTED, an upload would be refused: %s" % e["why"]))
    w("\nREFUSED, BY WHAT THE FILE NEEDS (a file that needs two things is in both lists; the effects above are counted under filter)\n")
    refused = [r for r in rows if not r["translates"]]
    for f in FEATURES:
        hits = [r for r in refused if f in r["needs"]]
        w("\n%s: %d files\n" % (f, len(hits)))
        for r in hits:
            more = [n for n in r["needs"] if n != f]
            w("  %-34s%s%s\n" % (r["file"], (" also: " + ", ".join(more)) if more else "",
                                 ("  [%s]" % r["checks"][:90]) if f == "checks" else ""))
    w("\nWHAT EACH MISSING FEATURE WOULD UNLOCK\n")
    u = unlocks(rows)
    for f in sorted(FEATURES, key=lambda f: -len(u[f]["alone"])):
        w("  %-11s alone: %3d   together with other features: %3d\n" % (f, len(u[f]["alone"]), len(u[f]["with_others"])))
    combos = {}
    for r in refused:
        combos.setdefault(" + ".join(r["needs"]), []).append(r["file"])
    w("\nBY EXACT COMBINATION\n")
    for k in sorted(combos, key=lambda k: -len(combos[k])):
        w("  %3d  %s\n" % (len(combos[k]), k))
    w("\nTHE TRANSLATOR'S FIRST REASON (what an upload would be told)\n")
    reasons = {}
    for r in refused:
        key = re.sub(r"input \w+ ", "input ... ", r["refused"])
        key = re.sub(r"the (?:input )?name \w+ ", "the name ... ", key)
        key = re.sub(r"\(found .*\)", "(found ...)", key)
        reasons[key] = reasons.get(key, 0) + 1
    for k in sorted(reasons, key=lambda k: -reasons[k]):
        w("  %3d  %s\n" % (reasons[k], k))


def main(argv):
    if len(argv) < 2 or not os.path.isdir(argv[1]):
        sys.stderr.write(__doc__)
        return 2
    rows = survey(argv[1])
    if "--json" in argv[2:]:
        json.dump({"files": rows, "unlocks": unlocks(rows)}, sys.stdout, indent=1, sort_keys=True)
        sys.stdout.write("\n")
    else:
        report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
