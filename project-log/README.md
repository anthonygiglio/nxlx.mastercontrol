<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Project log

Where the project remembers itself, so a new person or a new claude session can pick up cold, and so you can look back and see why something is the way it is.

Since 2026-10-24 (D107) every pull request writes **files of its own** and never edits a shared one, so two open pull requests no longer conflict on the log.

| Where | What goes in it | How |
| --- | --- | --- |
| [journal/](journal/) | Dated record of what was done, what merged, what is open | One file a pull request, `YYYY-MM-DD-name.md`; its entries newest first, each `## YYYY-MM-DD, what was done` |
| [lessons/](lessons/) | Something that went wrong or surprised us, and what to do next time | One file a pull request, `YYYY-MM-DD-name.md`; each lesson `## What we learned (date)` |
| [decisions/](decisions/) | A choice with lasting effect: what, why, what it costs, what it rules out | One file a decision, `DN-name.md`, heading `## DN. What was decided (date)`. To add to any decision, old or new: `DN-addendum-YYYY-MM-DD-name.md`, heading `## Addendum to DN (date): what` |
| [JOURNAL.md](JOURNAL.md), [LESSONS.md](LESSONS.md), [DECISIONS.md](DECISIONS.md) | The history up to 2026-10-24 (D1 to D106) | Never edited again; each has a pointer at its top |
| [../HANDOFF.md](../HANDOFF.md) | Current state in one page | Keep it true; rewrite it when things change |

## Writing an entry

1. `python3 tools/project-log.py new journal` makes today's journal file, named after your branch (`new lesson`, `new decision NAME`, `new addendum D106` likewise; `new decision` takes the next free number).
2. Write where it says WRITE-HERE. Keep the two SPDX lines at the top. More entries later in the same pull request (a review's fixes) go at the top of the same file.
3. `git fetch origin && python3 tools/project-log.py check --base origin/master` says what is wrong, in words, before CI does.

The name is small letters, digits and dashes, the branch's by habit. The date in a file's name is the day it was started (a journal file's name must be the date of one of its entries). A file of an earlier pull request is not edited or deleted: add a new one, or an addendum for a decision.

## Reading it as one

- `python3 tools/project-log.py journal`: the whole journal, newest first (the files in journal/, then JOURNAL.md).
- `python3 tools/project-log.py lessons`: every lesson, LESSONS.md first, then the files, oldest first.
- `python3 tools/project-log.py decisions`, or `... decisions D106 D107`: each decision followed by its addenda.
- `grep -r D106 project-log` finds a decision and every addendum, since the number is in the names and the headings.

Nothing combined is committed: a generated file that every pull request changes would bring the conflicts back.

## The check "project-log"

CI's check "project-log" (`.github/workflows/project-log.yml`) runs `tools/project-log.py check` on every pull request. It fails when the pull request:

- edits JOURNAL.md, LESSONS.md or DECISIONS.md (only the pointer at the top could be added, once, and it has been);
- edits or deletes a log file of an earlier pull request;
- adds a file with a bad name or date, no SPDX header, a heading of the wrong form, or the template's WRITE-HERE left in;
- gives a new decision a number master already has, or that another file has (it says the next free one: rename the file and its heading);
- adds an addendum to a decision that does not exist;
- adds **no journal file**. A trivial change (a typo, a version bump) may have the label `no-journal` instead.

## Rules of thumb

- Log the reason, not just the fact. "Chose X" is useless in six months; "Chose X because Y, at the cost of Z" is not.
- Record what was **not** verified as loudly as what was. Say "not run on a real board" when that is the case.
- Write it in the same pull request as the change. A decision made in chat and not logged is lost when the session ends.
- A decision is never rewritten: add an addendum, or a new decision that supersedes it.
- Plain language, no em dashes.
