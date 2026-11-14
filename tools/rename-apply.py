#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Apply the rename table (tools/rename-map.txt) to a checkout: the old pocketvj / pvj names become the NXLX and
Mastercontrol names, one kind at a time (rename-pvj-assessment.md, PR 1; decision D135).

    python3 tools/rename-apply.py                      a dry run of every kind: what would change, nothing written
    python3 tools/rename-apply.py --kind protocol      a dry run of one kind (repeat --kind for more)
    python3 tools/rename-apply.py --kind package --write    rewrite the files and move the renamed ones
    python3 tools/rename-apply.py --root ../other-tree      the same for another checkout (to bring a draft along)
    python3 tools/rename-apply.py --json               the report as JSON
    python3 tools/rename-apply.py --files              the report with a line for every file
    python3 tools/rename-apply.py --write-allow        recount tools/rename-allow.txt (the guard's list) from the tree

A dry run is the default and writes nothing. The report says how many places and files each kind would change,
which files would be renamed, which old-style names no row covers yet (a later pull request adds rows for them),
and which new names are already in the tree (a clash to look at before writing).

Every row of the table is matched in every file, also the rows of kinds that are not chosen: a row of another kind
still holds its own text, so a shorter row cannot take part of it (applying "render" leaves the CI job name
"name: pvj-render (" alone, which is "ci"). The matching rules are written at the top of the table.

Never rewritten: the credit, the upstream code and the upstream manual, which stay the credited original (SKIP
below), and the rename's own files, which must name the old names. Files are listed by git when the root is a git
checkout (untracked files are left alone), by walking the folder otherwise. A file with a NUL byte in its first
8 KiB, or that is not UTF-8, is not rewritten; its path still is.

Standard library only. It changes no behaviour by itself; each pull request of the rename runs it for its kinds.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join("tools", "rename-map.txt")
ALLOW = os.path.join("tools", "rename-allow.txt")
KINDS = ("text", "protocol", "render", "package", "system", "release", "env", "ci")
KEEP = "keep"
# What counts as an old-style name, for the guard and for "not covered": any letter case, so PVJ_DIR, Pvj and
# "Pocket VJ" are found too. Wider than the table on purpose: it also finds names no row covers yet.
OLD = re.compile(r"pvj|pocket[ _-]?vj", re.I)
# What every new name holds, the clash check's anchor (the same any-case search; boundaries come from the rows).
NEW = re.compile(r"nxlx|mctl|mastercontrol", re.I)
IDENT = "A-Za-z0-9_"
# Never rewritten by this script. The credit (LICENSE, AUTHORS, NOTICE); the upstream code, its old panel and the
# upstream manual as they were at the last upstream commit (ed74df4), kept as the credited original (decision B);
# RELEASE_NOTES, whose notes are the upstream's; and security.php, the fork's hardening of the old PHP panel, which
# shares the pvj_ function names and the X-PVJ-Request header with the upstream files it guards, so the two must
# move together or not at all.
SKIP = ("LICENSE.md", "AUTHORS.md", "NOTICE.md", "RELEASE_NOTES.md",
        "index.html", "backend.php", "security.php", "submit_*.php", "time_change.php", "offline.min.js", "favicon.ico",
        "sync/*", "js/*", "assets/*", "themes/*",
        "docs/html/*", "docs/index.html", "docs/build.md", "docs/Install_OSC_on35_or_older.md", "docs/OSC_Commands.md",
        "docs/PureData_install.md", "docs/TouchOSC_Layout V.01.md", "docs/projector_passwords.md", "docs/PVJ_2019.svg")
# The rename's own files name the old names on purpose: never rewritten, and not counted by the guard.
OWN = ("tools/rename-map.txt", "tools/rename-apply.py", "tools/rename-allow.txt", "tests/test_old_names.py")
# The kinds whose rename pull request has landed. The guard enforces only these: it counts only the matches of their
# rows, so an unrelated pull request that adds a pvj module, a PVJ_ variable or a journal line is not blocked before
# that kind's rename. Every rename pull request adds its kind here and recounts the allow list (--write-allow).
# Empty in PR 1: until a kind lands, its names are reported (the dry run, the guard's note) and not enforced.
LANDED = ()


class Row:
    __slots__ = ("old", "new", "kind", "note", "line", "family", "lit")

    def __init__(self, old, new, kind, note, line):
        self.old, self.new, self.kind, self.note, self.line = old, new, kind, note, line
        self.family = old.endswith("*")
        self.lit = old[:-1] if self.family else old

    def pattern(self):
        """The regular expression of this row, with the boundary rules of the table's header."""
        lit = self.lit
        if lit[0] in "/":
            before = "(?<![%s.])" % IDENT                  # a path segment (../pvj, code/pvj) is not an OSC address
        elif re.match("[%s]" % IDENT, lit[0]):
            before = "(?<![%s])" % IDENT
        else:
            before = ""
        if self.family:
            return before + re.escape(lit) + "[%s]*" % IDENT
        after = "(?![%s-])" % IDENT if re.match("[%s]" % IDENT, lit[-1]) else ""
        return before + re.escape(lit) + after

    def replace(self, text):
        return self.new[:-1] + text[len(self.lit):] if self.family else self.new


def load(path=None):
    """The rows of the table, checked: four fields, a known kind, no old name twice, a family on both sides or
    neither, a keep row unchanged and every other row changed. Raises ValueError naming the line."""
    path = path or os.path.join(ROOT, MAP)
    rows, seen = [], {}
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                raise ValueError("line %d: four fields separated by tabs are needed, found %d" % (n, len(parts)))
            old, new, kind, note = parts
            if kind not in KINDS + (KEEP,):
                raise ValueError("line %d: unknown kind %r" % (n, kind))
            if not old or not new or old == "*":
                raise ValueError("line %d: an empty name" % n)
            if old in seen:
                raise ValueError("line %d: %r is already on line %d" % (n, old, seen[old]))
            if old.endswith("*") != new.endswith("*"):
                raise ValueError("line %d: a family ends in * on both sides" % n)
            if (kind == KEEP) != (old == new):
                raise ValueError("line %d: a keep row keeps its name, and only a keep row does" % n)
            seen[old] = n
            rows.append(Row(old, new, kind, note, n))
    return rows


class Matcher:
    """All rows in one expression, longest first, so the longest row wins where two start at the same place.

    Every row holds an old-style name (OLD; the anchor), so the expression is only tried a little before each place
    the anchor finds, never at every character of a file: the same result as scanning the whole text, in a fraction
    of the time."""

    def __init__(self, rows, anchor=None):
        self.anchor = anchor or OLD
        self.rows = sorted(rows, key=lambda r: (-len(r.lit), r.old))
        self.rx = re.compile("|".join("(%s)" % r.pattern() for r in self.rows)) if self.rows else None
        reach = [self.anchor.search(r.lit) for r in self.rows]
        if any(x is None for x in reach):
            bad = [r.old for r, x in zip(self.rows, reach) if x is None]
            raise ValueError("a row without %s in it cannot be found: %s" % (self.anchor.pattern, ", ".join(bad)))
        self.reach = max([x.start() for x in reach] or [0])    # how far before the old-style name a row may start

    def matches(self, text):
        """[(start, end, row)] of every row's match, left to right, none overlapping."""
        out, at = [], 0
        if self.rx is None:
            return out
        for m in self.anchor.finditer(text):
            if m.start() < at:
                continue
            for s in range(max(at, m.start() - self.reach), m.start() + 1):
                mm = self.rx.match(text, s)
                if mm and mm.end() > m.start():
                    out.append((s, mm.end(), self.rows[mm.lastindex - 1]))
                    at = mm.end()
                    break
        return out

    def apply(self, text, kinds):
        """(new text, [(row, old text) of each change]) for the chosen kinds; other rows hold their text."""
        out, changes, at = [], [], 0
        for s, e, row in self.matches(text):
            if row.kind in kinds:
                out.append(text[at:s])
                out.append(row.replace(text[s:e]))
                changes.append((row, text[s:e]))
                at = e
        out.append(text[at:])
        return "".join(out), changes

    def uncovered(self, text):
        """[old-style text] of the places no row covers, of any kind."""
        spans = [(s, e) for s, e, _ in self.matches(text)]
        out, i = [], 0
        for m in OLD.finditer(text):
            while i < len(spans) and spans[i][1] <= m.start():
                i += 1
            if not (i < len(spans) and spans[i][0] <= m.start() and m.end() <= spans[i][1]):
                lo = m.start()
                while lo > 0 and re.match("[%s.-]" % IDENT, text[lo - 1]):
                    lo -= 1
                hi = m.end()
                while hi < len(text) and re.match("[%s-]" % IDENT, text[hi]):
                    hi += 1
                out.append(text[lo:hi])
        return out


def skipped(path):
    return path in OWN or any(fnmatch.fnmatchcase(path, p) for p in SKIP)


def files(root):
    """Every file of the checkout, relative, with / between folders."""
    if os.path.exists(os.path.join(root, ".git")):
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True).stdout
        return sorted(p for p in out.split("\0") if p and os.path.isfile(os.path.join(root, p)))
    found = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for n in names:
            found.append(os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/"))
    return sorted(found)


def read_text(root, path):
    """The file's text, or None when it is binary or not UTF-8."""
    with open(os.path.join(root, path), "rb") as f:
        data = f.read()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def old_count(path, text):
    """How many old-style names a file holds, in its text and its path, of any kind: what is reported."""
    return len(OLD.findall(path)) + (len(OLD.findall(text)) if text else 0)


def guard_count(matcher, path, text, landed):
    """How many names of the landed kinds a file holds, in its text and its path: the number the guard compares
    with the allow list. Every row is matched, so a row of a kind that has not landed still holds its text."""
    if not landed:
        return 0
    return sum(1 for t in (path, text) if t for _, _, row in matcher.matches(t) if row.kind in landed)


def outside(root, path):
    """Whether a path of the tree is a link, or leads out of the tree through one: never read, written or renamed
    (a link in another checkout could otherwise make --write change a file anywhere)."""
    full = os.path.join(root, path)
    real = os.path.realpath(full)
    return os.path.islink(full) or not real.startswith(os.path.realpath(root) + os.sep)


def plan(root, rows, kinds):
    """What applying the kinds would do, as a dict; nothing is written."""
    m = Matcher(rows)
    # A new name that is already in the tree: two things would then share a name (node-01 has a login group
    # "nxlx", which is why the shared group is nxlx-svc). Counted with the same boundaries, so "systemctl" is
    # never taken for "mctl".
    probe = Matcher([Row(n, n, KEEP, "", 0) for n in sorted({r.new for r in rows if r.kind in kinds and not r.family})],
                    anchor=NEW)
    report = {"kinds": list(kinds), "files": {}, "renames": {}, "uncovered": {}, "clashes": {},
              "skipped": 0, "not_text": [], "links": []}
    for path in files(root):
        if skipped(path):
            report["skipped"] += 1
            continue
        if outside(root, path):
            report["links"].append(path)
            continue
        text = read_text(root, path)
        if text is None:
            if OLD.search(path):
                report["not_text"].append(path)
        else:
            new, changes = m.apply(text, kinds)
            if changes:
                report["files"][path] = changes
            left = m.uncovered(text)
            if left:
                report["uncovered"][path] = left
            if probe.rows:
                for _, _, row in probe.matches(text):
                    report["clashes"][row.new] = report["clashes"].get(row.new, 0) + 1
        newpath, pchanges = m.apply(path, kinds)
        if pchanges:
            report["renames"][path] = newpath
        if OLD.search(path):
            left = m.uncovered(path)
            if left:
                report["uncovered"].setdefault(path, []).extend(left)
    targets = {}
    for old, new in report["renames"].items():
        targets.setdefault(new, []).append(old)
    report["rename_collisions"] = {n: o for n, o in targets.items() if len(o) > 1 or os.path.exists(os.path.join(root, n))}
    return report


def summary(report):
    """The headline numbers of a plan."""
    places = sum(len(c) for c in report["files"].values())
    names = {old for c in report["files"].values() for _, old in c}
    by_kind = {}
    for path, changes in report["files"].items():
        for row, _ in changes:
            k = by_kind.setdefault(row.kind, {"places": 0, "files": set(), "rows": set()})
            k["places"] += 1
            k["files"].add(path)
            k["rows"].add(row.old)
    return {"places": places, "distinct_names": len(names), "files": len(report["files"]),
            "renames": len(report["renames"]),
            "uncovered_places": sum(len(v) for v in report["uncovered"].values()),
            "uncovered_files": len(report["uncovered"]),
            "by_kind": {k: {"places": v["places"], "files": len(v["files"]), "rows": len(v["rows"])}
                        for k, v in sorted(by_kind.items(), key=lambda kv: KINDS.index(kv[0]))},
            "skipped_files": report["skipped"]}


def print_report(report, every_file=False, out=None, writing=False):
    s = summary(report)
    w = (out or sys.stdout).write
    w("%s Kinds: %s\n\n" % ("Rename, writing." if writing else "Rename dry run (nothing written).",
                            ", ".join(report["kinds"])))
    w("Would change %d places (%d distinct names) in %d files, and rename %d files.\n"
      % (s["places"], s["distinct_names"], s["files"], s["renames"]))
    w("Not covered by any row yet: %d places in %d files.\n" % (s["uncovered_places"], s["uncovered_files"]))
    w("Not looked at (the credit, the upstream original, the rename's own files): %d files.\n\n" % s["skipped_files"])
    w("%-10s %8s %7s %6s\n" % ("kind", "places", "files", "rows"))
    for k, v in s["by_kind"].items():
        w("%-10s %8d %7d %6d\n" % (k, v["places"], v["files"], v["rows"]))
    if report["renames"]:
        w("\nFiles renamed (first 20 of %d):\n" % len(report["renames"]))
        for old, new in sorted(report["renames"].items())[:20]:
            w("  %s -> %s\n" % (old, new))
    if report["rename_collisions"]:
        w("\nRENAME COLLISIONS (a target exists or is taken twice):\n")
        for new, olds in sorted(report["rename_collisions"].items()):
            w("  %s <- %s\n" % (new, ", ".join(olds)))
    if report["clashes"]:
        w("\nNew names already in the tree (look before writing):\n")
        for name, c in sorted(report["clashes"].items(), key=lambda kv: -kv[1]):
            w("  %-28s %d\n" % (name, c))
    if report["uncovered"]:
        tokens = {}
        for v in report["uncovered"].values():
            for t in v:
                tokens[t] = tokens.get(t, 0) + 1
        w("\nNot covered, the most frequent (first 25 of %d distinct):\n" % len(tokens))
        for t, c in sorted(tokens.items(), key=lambda kv: (-kv[1], kv[0]))[:25]:
            w("  %-40s %d\n" % (t, c))
    if report["not_text"]:
        w("\nBinary or not UTF-8, only the path is looked at: %d files.\n" % len(report["not_text"]))
    if report["links"]:
        w("\nLinks, never followed, rewritten or renamed: %d (%s).\n" % (len(report["links"]), ", ".join(report["links"][:5])))
    if every_file:
        w("\nEvery file (places changed, places not covered):\n")
        for path in sorted(set(report["files"]) | set(report["uncovered"])):
            w("  %5d %5d  %s\n" % (len(report["files"].get(path, [])), len(report["uncovered"].get(path, [])), path))


def as_json(report):
    s = summary(report)
    s["kinds"] = report["kinds"]
    s["renames_list"] = report["renames"]
    s["rename_collisions"] = report["rename_collisions"]
    s["clashes"] = report["clashes"]
    s["files_list"] = {p: len(c) for p, c in report["files"].items()}
    s["uncovered_list"] = report["uncovered"]
    return json.dumps(s, indent=1, sort_keys=True)


def write(root, rows, kinds, report):
    """Rewrite the files and move the renamed ones. Refuses before writing anything when a rename would land on a
    file that exists or two files would get one name."""
    if report["rename_collisions"]:
        raise SystemExit("not written: rename collisions, see the dry run")
    m = Matcher(rows)
    for path in report["files"]:
        if outside(root, path):             # looked at again: it may have become a link since the plan
            continue
        full = os.path.join(root, path)
        text = read_text(root, path)
        new, _ = m.apply(text, kinds)
        mode = os.stat(full).st_mode
        fd = os.open(full, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.chmod(full, mode)
    git = os.path.exists(os.path.join(root, ".git"))
    for old, new in sorted(report["renames"].items()):
        if outside(root, old):
            continue
        os.makedirs(os.path.dirname(os.path.join(root, new)) or root, exist_ok=True)
        if git:
            subprocess.run(["git", "mv", "--", old, new], cwd=root, check=True, capture_output=True)
        else:
            os.renames(os.path.join(root, old), os.path.join(root, new))


def table_of(root):
    """The rows of the root's table, or of this checkout's when the root has none (a throwaway folder)."""
    own = os.path.join(root, MAP)
    return load(own if os.path.exists(own) else None)


def allow_counts(root, landed=None):
    """{path: count} of every file that holds names of the landed kinds (LANDED unless given), except the rename's
    own files. Links are not read."""
    landed = LANDED if landed is None else tuple(landed)
    if not landed:
        return {}
    m = Matcher(table_of(root))
    out = {}
    for path in files(root):
        if path in OWN or outside(root, path):
            continue
        c = guard_count(m, path, read_text(root, path), landed)
        if c:
            out[path] = c
    return out


def old_counts(root):
    """{path: count} of every file that holds old-style names of any kind (the report), except the rename's own
    files. Links are not read."""
    out = {}
    for path in files(root):
        if path in OWN or outside(root, path):
            continue
        c = old_count(path, read_text(root, path))
        if c:
            out[path] = c
    return out


def read_allow(root):
    """{path: ceiling} from tools/rename-allow.txt."""
    out = {}
    with open(os.path.join(root, ALLOW), encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            count, _, path = line.partition("\t")
            if not count.isdigit() or not path:
                raise ValueError("%s line %d: a count, a tab and a path" % (ALLOW, n))
            out[path] = int(count)
    return out


ALLOW_HEAD = """# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
#
# The guard's allow list (tests/test_old_names.py): every file that may still hold old names of the kinds whose
# rename has landed (LANDED in tools/rename-apply.py), and at most how many (matches of those kinds' rows, in its
# text and its path together). A file not listed may hold none. A file may go below its number. Each rename pull
# request adds its kind to LANDED and recounts the list with
#     python3 tools/rename-apply.py --write-allow
# Raising a number by hand is a visible choice for review: say why in the pull request.
# Empty while no kind has landed. At the end of the rename only the credit and the upstream original are left.
#
# count\tpath
"""


def write_allow(root, landed=None):
    counts = allow_counts(root, landed)
    with open(os.path.join(root, ALLOW), "w", encoding="utf-8") as f:
        f.write(ALLOW_HEAD)
        for path in sorted(counts):
            f.write("%d\t%s\n" % (counts[path], path))
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", action="append", choices=KINDS, help="a kind to apply (repeatable; all when left out)")
    ap.add_argument("--root", default=ROOT, help="the checkout to work on (this one by default)")
    ap.add_argument("--map", help="the table (the root's tools/rename-map.txt, else this checkout's)")
    ap.add_argument("--write", action="store_true", help="rewrite and rename; without it nothing is written")
    ap.add_argument("--json", action="store_true", help="the report as JSON")
    ap.add_argument("--files", action="store_true", help="a line for every file in the report")
    ap.add_argument("--write-allow", action="store_true", help="recount the guard's allow list from the tree, and stop")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if a.write_allow:
        counts = write_allow(root)
        print("%s: %d files, %d names" % (ALLOW, len(counts), sum(counts.values())))
        return 0
    table = a.map or (os.path.join(root, MAP) if os.path.exists(os.path.join(root, MAP)) else os.path.join(ROOT, MAP))
    rows = load(table)
    kinds = tuple(a.kind) if a.kind else KINDS
    report = plan(root, rows, kinds)
    if a.json:
        print(as_json(report))
    else:
        print_report(report, every_file=a.files, writing=a.write)
    if a.write:
        write(root, rows, kinds, report)
        print("\nWritten: %d files changed, %d renamed." % (len(report["files"]), len(report["renames"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
