#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The project log, one new file per pull request (D107).

    python3 tools/project-log.py journal                 the whole journal, newest first: the files in journal/, then JOURNAL.md
    python3 tools/project-log.py lessons                 every lesson: LESSONS.md, then the files in lessons/, oldest first
    python3 tools/project-log.py decisions [D106 ...]    every decision with its addenda joined, or only the ones named
    python3 tools/project-log.py next                    the next free decision number
    python3 tools/project-log.py new journal [NAME]      start today's journal file (NAME is the branch's name by default)
    python3 tools/project-log.py new lesson [NAME]       start today's lesson file
    python3 tools/project-log.py new decision NAME       start a new decision, with the next free number
    python3 tools/project-log.py new addendum D106 [NAME]    start an addendum to D106
    python3 tools/project-log.py check [--base REF] [--no-journal-ok]

Why: every pull request used to add to the top of JOURNAL.md, and often to LESSONS.md and DECISIONS.md, so any two
open pull requests conflicted on them, and every merge sent the others back to merge master and run CI again. Two
pull requests also took the same "next D". Now JOURNAL.md, LESSONS.md and DECISIONS.md are history: each keeps a
short pointer at its top and is never edited again. A pull request adds files of its own instead:

    project-log/journal/YYYY-MM-DD-NAME.md               its journal entry or entries, newest first, as JOURNAL.md had them
    project-log/lessons/YYYY-MM-DD-NAME.md               its lessons, as LESSONS.md had them
    project-log/decisions/DN-NAME.md                     one new decision, its heading "## DN. What (date)"
    project-log/decisions/DN-addendum-YYYY-MM-DD-NAME.md an addendum to DN (old or new), its heading "## Addendum to DN ..."

Two new files never conflict. Nothing combined is written to the tree, since that would bring the conflicts back:
this tool prints the whole log when someone wants to read it as one document.

`check` is what CI's "project-log" check runs (.github/workflows/project-log.yml). Without --base it looks at the
files as they are: names, dates, headings, the SPDX header, UTF-8, a number used twice (CI runs this much on every
push to master, so two pull requests that took one number and both merged show red at once). With --base (CI gives
HEAD^1, the tip of the base branch that GitHub's test merge sits on; by hand, origin/master after a fetch) it also
looks at what this branch changed since it left REF: the three old files edited (the pointer may be added once,
nothing else; once the base branch has it, the files must be the base branch's), an older log file edited or deleted
(but for moving a number that the base branch has twice to a new one), no journal file added (unless
--no-journal-ok, which CI passes for the label "no-journal"), and a new decision whose number the base branch
already has. Every refusal says in words what to do.

Standard library only; git is needed for --base and for the order of files written on the same day.
"""
import datetime
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = "project-log"
JOURNAL, LESSONS, DECISIONS = "JOURNAL.md", "LESSONS.md", "DECISIONS.md"
FROZEN = (JOURNAL, LESSONS, DECISIONS)
FOLDERS = ("journal", "lessons", "decisions")
START = "<!-- log-fragments: pointer -->"
END = "<!-- /log-fragments: pointer -->"
POINTER = re.compile(re.escape(START) + r"\n.*?" + re.escape(END) + r"\n\n?", re.S)
SPDX = ("<!-- SPDX-FileCopyrightText: %d NXLX.Systems and contributors\n"
        "     SPDX-License-Identifier: Apache-2.0 -->\n")
TEMPLATE = "WRITE-HERE"                     # what `new` leaves to be written; the check refuses a file that has it
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
DATE = r"\d{4}-\d{2}-\d{2}"
DAY_FILE = re.compile(r"(%s)-(%s)\.md" % (DATE, SLUG))                      # journal/ and lessons/
ADDENDUM_FILE = re.compile(r"D([1-9][0-9]*)-addendum-(%s)-(%s)\.md" % (DATE, SLUG))
DECISION_FILE = re.compile(r"D([1-9][0-9]*)-(%s)\.md" % SLUG)
OLD_DECISION = re.compile(r"^#{2,3} D([0-9]+)\.", re.M)                     # a decision's heading in DECISIONS.md
JOURNAL_HEAD = re.compile(r"(%s), \S" % DATE)
DECISION_HEAD = re.compile(r"D([0-9]+)\. \S")
ADDENDUM_HEAD = re.compile(r"Addendum to D([0-9]+)\b")
HOWTO = "How to write the log: project-log/README.md."
BEFORE = "An open pull request from before D107"           # the README's section for moving a branch over


def joined(parts):
    """The printed parts as one text, put one after the other unchanged, so the old files' entries come out byte for
    byte as the files have them. A part from a new file ends in one blank line (`spaced`)."""
    out = ""
    for part in parts:
        if out and not out.endswith("\n"):
            out += "\n"
        out += part
    return out


def spaced(text):
    """A part from a new file, ending in one blank line."""
    return text.rstrip("\n") + "\n\n"


# --- small helpers -----------------------------------------------------------------------------------------------

def read(path, strict=False):
    """A file's text. Bytes that are not UTF-8 raise UnicodeDecodeError when strict (the check says so in words),
    and are replaced otherwise (printing, and comparing, where a replaced byte is still a difference)."""
    with open(path, encoding="utf-8", errors="strict" if strict else "replace") as f:
        return f.read()


def shown(name):
    """A file name as it can be printed: as it is when it is a plain one, else with Python's quoting, so a carriage
    return or a line feed in a name can never start a line of its own (an annotation, in CI)."""
    return name if re.fullmatch(r"[A-Za-z0-9._/-]+", name) else repr(name)


def git(root, *args):
    """git's output, or None when git is not there or says no."""
    try:
        p = subprocess.run(("git",) + args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def show(root, ref, path):
    """The text of a file at a commit, or None when it is not there."""
    return git(root, "show", "%s:%s" % (ref, path))


def a_date(text):
    try:
        return datetime.date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return None


def body(text):
    """A file without its SPDX comment and the pointer, with one newline at the end."""
    if text.startswith("<!--") and "SPDX" in text[:text.find("-->") + 1]:
        text = text[text.find("-->") + 3:]
    text = POINTER.sub("", text)
    return text.strip("\n") + "\n"


def headings(text):
    """The `## ` headings of a text, without the hashes."""
    return [line[3:] for line in text.splitlines() if line.startswith("## ")]


def sections(text):
    """A text cut at its `## ` headings: [(heading, the section with its heading)], and what came before."""
    parts = re.split(r"(?m)^(?=## )", text)
    before = parts[0] if parts and not parts[0].startswith("## ") else ""
    return before, [(p.splitlines()[0][3:], p) for p in parts if p.startswith("## ")]


def files(root, folder):
    path = os.path.join(root, LOG, folder)
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def added_times(root, folder):
    """When each file of a folder was first committed (seconds), so files of the same day keep the order they were
    written in. A file not committed yet is the newest. Without git every file counts the same."""
    out = git(root, "log", "--diff-filter=A", "--name-only", "--format=@%ct", "--", "%s/%s" % (LOG, folder))
    times, t = {}, 0
    for line in (out or "").splitlines():
        if line.startswith("@"):
            t = int(line[1:])
        elif line.strip():
            times.setdefault(os.path.basename(line.strip()), t)
    return lambda name: times.get(name, float("inf")) if out is not None else 0


def old_numbers(text):
    return [int(n) for n in OLD_DECISION.findall(text or "")]


def decision_numbers(root):
    """Every decision number in the tree: {number: [where]}."""
    found = {}
    path = os.path.join(root, LOG, DECISIONS)
    if os.path.exists(path):
        for n in old_numbers(read(path)):
            found.setdefault(n, []).append("%s/%s" % (LOG, DECISIONS))
    for name in files(root, "decisions"):
        if not ADDENDUM_FILE.fullmatch(name) and DECISION_FILE.fullmatch(name):
            found.setdefault(int(DECISION_FILE.fullmatch(name).group(1)), []).append("%s/decisions/%s" % (LOG, name))
    return found


def base_places(root, ref):
    """Where the base branch (or any REF) has each decision number: {number: [where]}."""
    found = {}
    for n in old_numbers(show(root, ref, "%s/%s" % (LOG, DECISIONS))):
        found.setdefault(n, []).append("%s/%s" % (LOG, DECISIONS))
    for line in (git(root, "ls-tree", "--name-only", ref, "%s/decisions/" % LOG) or "").splitlines():
        name = os.path.basename(line)
        m = DECISION_FILE.fullmatch(name)
        if m and not ADDENDUM_FILE.fullmatch(name):
            found.setdefault(int(m.group(1)), []).append(line)
    return found


def base_numbers(root, ref):
    """The decision numbers the base branch (or any REF) has: {number: the first place it is}."""
    return {n: places[0] for n, places in base_places(root, ref).items()}


def next_number(root, ref=None):
    taken = set(decision_numbers(root))
    if ref and git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}") is not None:
        taken |= set(base_numbers(root, ref))
    return max(taken | {0}) + 1


# --- the check ---------------------------------------------------------------------------------------------------

def tree_problems(root, today=None):
    """What is wrong with the log's files as they are, in words."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    latest = today + datetime.timedelta(days=1)          # a writer west or east of UTC may be a day ahead of it
    bad = []

    def check_date(where, text):
        d = a_date(text)
        if d is None:
            bad.append("%s: %s is not a real date. Name the file by today's date, YYYY-MM-DD." % (where, text))
        elif d > latest:
            bad.append("%s: %s is in the future. Name the file by today's date." % (where, text))
        return d

    def check_text(where, text):
        if "SPDX-License-Identifier: Apache-2.0" not in text[:300]:
            bad.append("%s: it has no SPDX header. Start it with these two lines (D3):\n%s"
                       % (where, SPDX % today.year))
        if TEMPLATE in text:
            bad.append("%s: it still has %s from the template. Write the entry, or take the file out." % (where, TEMPLATE))

    for folder in FOLDERS:
        for name in files(root, folder):
            where = shown("%s/%s/%s" % (LOG, folder, name))
            path = os.path.join(root, LOG, folder, name)
            if not os.path.isfile(path):
                bad.append("%s: only files go in %s/%s/, not folders." % (where, LOG, folder))
                continue
            try:
                text = read(path, strict=True)
            except UnicodeDecodeError:
                bad.append("%s: it is not UTF-8 text. Save it as UTF-8 (plain text)." % where)
                continue
            if folder in ("journal", "lessons"):
                m = DAY_FILE.fullmatch(name)
                if not m:
                    bad.append("%s: a bad name. A file here is named YYYY-MM-DD-name.md: the date it was started, then "
                               "a short name in small letters, digits and dashes (the branch's, say), e.g. "
                               "%s/%s/%s-log-fragments.md." % (where, LOG, folder, today.isoformat()))
                    continue
                check_date(where, m.group(1))
                check_text(where, text)
                heads = headings(body(text))
                if not heads:
                    bad.append("%s: it has no entry. Each entry starts with a heading `## ...`." % where)
                if folder == "journal":
                    dates = []
                    for h in heads:
                        hm = JOURNAL_HEAD.match(h)
                        if not hm or a_date(hm.group(1)) is None:
                            bad.append("%s: the heading `## %s` is not a journal entry's. Each one is "
                                       "`## YYYY-MM-DD, what was done`, as in JOURNAL.md." % (where, h))
                        else:
                            dates.append(hm.group(1))
                    if dates and m.group(1) not in dates:
                        bad.append("%s: no entry in it is dated %s, the date in its name. Name it by the day of one "
                                   "of its entries." % (where, m.group(1)))
                    if dates != sorted(dates, reverse=True):
                        bad.append("%s: its entries are not newest first. Put the newest entry at the top." % where)
                continue
            # decisions/
            am, dm = ADDENDUM_FILE.fullmatch(name), DECISION_FILE.fullmatch(name)
            if not am and (not dm or dm.group(2).startswith("addendum")):
                bad.append("%s: a bad name. A new decision is DN-name.md (e.g. D107-log-fragments.md, its number "
                           "from `python3 tools/project-log.py next`); an addendum is DN-addendum-YYYY-MM-DD-name.md "
                           "(e.g. D106-addendum-%s-render-phase3e.md). Names are small letters, digits and dashes."
                           % (where, today.isoformat()))
                continue
            check_text(where, text)
            heads = headings(body(text))
            if am:
                n = int(am.group(1))
                check_date(where, am.group(2))
                hm = ADDENDUM_HEAD.match(heads[0]) if heads else None
                if not hm or int(hm.group(1)) != n:
                    bad.append("%s: its first heading must be `## Addendum to D%d (date): what`, so that "
                               "`grep -r D%d project-log` finds it." % (where, n, n))
            else:
                n = int(dm.group(1))
                hm = DECISION_HEAD.match(heads[0]) if heads else None
                if not hm or int(hm.group(1)) != n:
                    bad.append("%s: its first heading must be `## D%d. What was decided (date)`, the number of its "
                               "name." % (where, n))
                others = [h for h in heads[1:] if DECISION_HEAD.match(h)]
                if others:
                    bad.append("%s: one decision a file. `## %s` goes in a file of its own." % (where, others[0]))
    numbers = decision_numbers(root)
    for n in sorted(numbers):
        if len(numbers[n]) > 1:
            bad.append("D%d is used more than once: %s. Each decision has a number of its own; give the newer one "
                       "the next free number, D%d (`python3 tools/project-log.py next`), in its file's name and its "
                       "heading." % (n, " and ".join(numbers[n]), max(numbers) + 1))
    for name in files(root, "decisions"):
        am = ADDENDUM_FILE.fullmatch(name)
        if am and int(am.group(1)) not in numbers:
            bad.append("%s/decisions/%s: it is an addendum to D%s, and there is no D%s." % (LOG, shown(name),
                                                                                           am.group(1), am.group(1)))
    return bad


def diff_problems(root, base, no_journal_ok=False):
    """What this branch changed in the log since it left BASE, against the rules, in words."""
    if git(root, "rev-parse", "--verify", "--quiet", base + "^{commit}") is None:
        return ["--base %s: git does not know that commit here. Fetch it first (`git fetch origin`)." % base]
    since = (git(root, "merge-base", base, "HEAD") or "").strip()
    if not since:
        return ["--base %s: this branch has no commit in common with it." % base]
    changed = {}
    for line in (git(root, "diff", "--name-status", "--no-renames", since, "--", LOG) or "").splitlines():
        status, path = line.split("\t", 1)
        changed[path] = status[0]
    for path in (git(root, "ls-files", "--others", "--exclude-standard", "--", LOG) or "").splitlines():
        changed[path] = "A"
    bad = []
    for name in FROZEN:
        path = "%s/%s" % (LOG, name)
        if path not in changed:                 # untouched since the branch left the base: never this branch's fault
            continue
        if changed[path] == "D":
            bad.append("%s was deleted. It is the log's history and stays as it is." % path)
            continue
        old, new = show(root, since, path) or "", read(os.path.join(root, path))
        on_base = show(root, base, path)
        if START in old or (on_base is not None and START in on_base):
            # The base branch already has the pointer: the file must be the base branch's, byte for byte. A branch
            # from before D107 that has not merged it yet, or is in the middle of that merge, is judged the same.
            if on_base is None or new != on_base:
                bad.append("%s was edited. Since D107 it is history and is not edited; put what you wrote in a file "
                           "of your own instead (%s). A pull request opened before D107: see \"%s\" in "
                           "project-log/README.md." % (path, {JOURNAL: "project-log/journal/", LESSONS:
                           "project-log/lessons/", DECISIONS: "project-log/decisions/"}[name], BEFORE))
        elif new.count(START) != 1 or POINTER.sub("", new, count=1) != old:
            bad.append("%s was edited. Only the pointer at its top may be added to it, once, and nothing else in it "
                       "changed. %s" % (path, HOWTO))
    added = [p for p, s in changed.items() if s == "A"]
    places = base_places(root, base)
    for path, status in sorted(changed.items()):
        if status == "A" or path.split("/")[1:2] == [] or path.split("/")[1] not in FOLDERS:
            continue
        if status == "D" and renumbered(path, places, added):
            continue
        bad.append("%s was %s. It was written by an earlier pull request, and old entries are not changed: add a "
                   "new file instead (an addendum, for a decision)." % (shown(path), "deleted" if status == "D"
                                                                        else "edited"))
    if not no_journal_ok and not any(p.startswith(LOG + "/journal/") and p.endswith(".md") for p in added):
        bad.append("This pull request adds no journal entry. Add project-log/journal/YYYY-MM-DD-name.md "
                   "(`python3 tools/project-log.py new journal` starts one); a trivial change may have the label "
                   "no-journal instead.")
    on_base = base_numbers(root, base)
    for path in sorted(added):
        name = os.path.basename(path)
        m = DECISION_FILE.fullmatch(name)
        if not path.startswith(LOG + "/decisions/") or not m or ADDENDUM_FILE.fullmatch(name):
            continue
        n = int(m.group(1))
        if n in on_base and on_base[n] != path:
            bad.append("%s: D%d is already taken on the base branch (%s). Take the next free number, D%d: rename the "
                       "file and change its heading (`python3 tools/project-log.py next --base %s` gives it)."
                       % (shown(path), n, on_base[n], next_number(root, base), base))
    return bad


def renumbered(path, places, added):
    """Whether a deleted decision file is the renumbering of a number the base branch has twice: two pull requests
    took one number and both merged, and this one moves one of the two to a new number under the same name
    (D108-x.md deleted, D110-x.md added). Without this the fix the check asks for would itself be refused."""
    m = DECISION_FILE.fullmatch(os.path.basename(path))
    if not path.startswith(LOG + "/decisions/") or not m or ADDENDUM_FILE.fullmatch(os.path.basename(path)):
        return False
    n, slug = int(m.group(1)), m.group(2)
    if len(places.get(n, [])) < 2:
        return False
    for p in added:
        a = DECISION_FILE.fullmatch(os.path.basename(p))
        if (p.startswith(LOG + "/decisions/") and a and not ADDENDUM_FILE.fullmatch(os.path.basename(p))
                and a.group(2) == slug and int(a.group(1)) != n):
            return True
    return False


# --- reading the log as one -------------------------------------------------------------------------------------

def print_journal(root):
    out = ["# Journal\n\nNewest entry first. Printed by tools/project-log.py: the files in journal/ (D107), then "
           "JOURNAL.md.\n\n"]
    when = added_times(root, "journal")
    entries = []
    for name in files(root, "journal"):
        m = DAY_FILE.fullmatch(name)
        if not m:
            continue
        for i, (head, text) in enumerate(sections(body(read(os.path.join(root, LOG, "journal", name))))[1]):
            hm = JOURNAL_HEAD.match(head)
            entries.append(((hm.group(1) if hm else m.group(1)), when(name), name, -i, text))
    entries.sort(reverse=True)
    out += [spaced(e[-1]) for e in entries]
    path = os.path.join(root, LOG, JOURNAL)
    if os.path.exists(path):
        old = body(read(path))
        at = re.search(r"(?m)^## ", old)
        if at:
            out.append(old[at.start():])            # the old entries as one part: the file's own bytes
    return out


def print_lessons(root):
    path = os.path.join(root, LOG, LESSONS)
    out = [spaced(body(read(path)))] if os.path.exists(path) else []
    when = added_times(root, "lessons")
    names = [n for n in files(root, "lessons") if DAY_FILE.fullmatch(n)]
    for name in sorted(names, key=lambda n: (n[:10], when(n), n)):
        out.append(spaced(body(read(os.path.join(root, LOG, "lessons", name)))))
    return out


def print_decisions(root, wanted=None):
    """Every decision, each followed by its addenda (DECISIONS.md's own, then the files'), or only those wanted."""
    when = added_times(root, "decisions")
    addenda, new = {}, {}
    for name in files(root, "decisions"):
        text = spaced(body(read(os.path.join(root, LOG, "decisions", name))))
        am, dm = ADDENDUM_FILE.fullmatch(name), DECISION_FILE.fullmatch(name)
        if am:
            addenda.setdefault(int(am.group(1)), []).append((am.group(2), when(name), name, text))
        elif dm:
            new[int(dm.group(1))] = text
    out = []
    path = os.path.join(root, LOG, DECISIONS)
    before, parts = sections(body(read(path))) if os.path.exists(path) else ("", [])
    if wanted is None and before.strip():
        out.append(before)
    seen = set()

    def one(n, text):
        if wanted is None or n in wanted:
            seen.add(n)
            out.append(text)
            out.extend(a[-1] for a in sorted(addenda.get(n, [])))

    for head, text in parts:
        m = DECISION_HEAD.match(head)
        if m:
            one(int(m.group(1)), text)
        elif wanted is None:
            out.append(text)
    for n in sorted(new):
        one(n, new[n])
    return out, (set(wanted or ()) - seen)


# --- starting a file ------------------------------------------------------------------------------------------------

def branch_slug(root):
    name = (git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "work"


def new_file(root, kind, args, today):
    day = today.isoformat()
    if kind in ("journal", "lesson"):
        if len(args) > 1:
            return None, "usage: new %s [NAME]" % kind
        name = args[0] if args else branch_slug(root)
        folder = "journal" if kind == "journal" else "lessons"
        rel = "%s/%s/%s-%s.md" % (LOG, folder, day, name)
        text = ("## %s, %s\n\n- %s\n" % (day, TEMPLATE, TEMPLATE) if kind == "journal"
                else "## %s (%s)\n\n%s: what happened, and what we do now.\n" % (TEMPLATE, day, TEMPLATE))
    elif kind == "decision":
        if len(args) != 1:
            return None, "usage: new decision NAME"
        n, name = next_number(root, "origin/master"), args[0]
        rel = "%s/decisions/D%d-%s.md" % (LOG, n, name)
        text = ("## D%d. %s (%s)\n\n%s. Why: %s. Cost / rules out: %s.\n" % (n, TEMPLATE, day, TEMPLATE, TEMPLATE, TEMPLATE))
    elif kind == "addendum":
        if not args or len(args) > 2 or not re.fullmatch(r"D[1-9][0-9]*", args[0]):
            return None, "usage: new addendum DN [NAME]"
        n = int(args[0][1:])
        if n not in decision_numbers(root):
            return None, "there is no D%d" % n
        name = args[1] if len(args) > 1 else branch_slug(root)
        rel = "%s/decisions/D%d-addendum-%s-%s.md" % (LOG, n, day, name)
        text = "## Addendum to D%d (%s): %s\n\n%s\n" % (n, day, TEMPLATE, TEMPLATE)
    else:
        return None, "new: journal, lesson, decision or addendum"
    if not re.fullmatch(SLUG, name) or name.startswith("addendum"):
        return None, "a name is small letters, digits and dashes (and does not start with addendum): %s" % name
    path = os.path.join(root, rel)
    if show(root, "origin/master", rel) is not None:
        return None, ("%s is there already: an earlier pull request wrote it. Give a NAME of your own, e.g. "
                      "`python3 tools/project-log.py new %s %s%s-2`" % (rel, kind, args[0] + " " if kind == "addendum"
                                                                        else "", name))
    if os.path.exists(path):
        return None, "%s is there already: this branch started it, so add to it" % rel
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(SPDX % today.year + text)
    return rel, None


# --- the command -------------------------------------------------------------------------------------------------

USAGE = __doc__.split("\n\n")[1]


def main(argv, root=None, today=None, out=None, err=None):
    root = root or ROOT
    # `new` dates a file by the writer's own day (the owner's evening is already tomorrow in UTC); the check allows a
    # day ahead of UTC for that reason.
    local_today = today or datetime.date.today()
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    out, err = out or sys.stdout, err or sys.stderr
    args = argv[1:]
    cmd = args[0] if args else ""
    if cmd == "journal" and len(args) == 1:
        out.write(joined(print_journal(root)))
        return 0
    if cmd == "lessons" and len(args) == 1:
        out.write(joined(print_lessons(root)))
        return 0
    if cmd == "decisions":
        wanted = None
        if len(args) > 1:
            if not all(re.fullmatch(r"[Dd][1-9][0-9]*", a) for a in args[1:]):
                err.write("decisions: name them as D106 D107\n")
                return 2
            wanted = {int(a[1:]) for a in args[1:]}
        parts, missing = print_decisions(root, wanted)
        out.write(joined(parts))
        if missing:
            err.write("no such decision: %s\n" % ", ".join("D%d" % n for n in sorted(missing)))
            return 1
        return 0
    if cmd == "next" and (len(args) == 1 or (len(args) == 3 and args[1] == "--base")):
        out.write("D%d\n" % next_number(root, args[2] if len(args) == 3 else "origin/master"))
        return 0
    if cmd == "new" and len(args) >= 2:
        rel, why = new_file(root, args[1], args[2:], local_today)
        if why:
            err.write(why + "\n")
            return 2
        out.write("%s\nWrite in it where it says %s.\n" % (rel, TEMPLATE))
        return 0
    if cmd == "check":
        base, ok_without, rest = None, False, args[1:]
        while rest:
            if rest[0] == "--base" and len(rest) > 1:
                base, rest = rest[1], rest[2:]
            elif rest[0] == "--no-journal-ok":
                ok_without, rest = True, rest[1:]
            else:
                err.write("usage: check [--base REF] [--no-journal-ok]\n")
                return 2
        bad = tree_problems(root, today) + (diff_problems(root, base, ok_without) if base else [])
        annotate = os.environ.get("GITHUB_ACTIONS") == "true"
        for line in bad:
            # One problem, one line in CI: no line feed or carriage return (or any other control character) of a
            # name can start a line of its own there, which GitHub would read as an annotation of its own.
            line = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", " ", line)
            out.write(("::error::%s\n" % line.replace("\n", " ")) if annotate else ("project-log: %s\n" % line))
        if bad:
            out.write("project-log: %d problem(s). %s\n" % (len(bad), HOWTO))
            return 1
        out.write("project-log: the log's files are in order%s.\n" % (" and so is what this branch changed in them"
                                                                   if base else ""))
        return 0
    err.write("usage:\n%s\n" % USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
