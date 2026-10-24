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

## An open pull request from before D107

A branch that still adds to JOURNAL.md, LESSONS.md or DECISIONS.md moves its additions into files of its own:

1. Before merging master, list what the branch added to the three files, and keep the list:
   `git fetch origin && git diff origin/master...HEAD -- project-log/JOURNAL.md project-log/LESSONS.md project-log/DECISIONS.md`
2. Merge master (`git merge origin/master`), then take master's copies of the three files, whatever the merge did to them:
   `git checkout origin/master -- project-log/JOURNAL.md project-log/LESSONS.md project-log/DECISIONS.md`
3. Start the new files with `python3 tools/project-log.py new journal`, `new lesson`, and `new decision NAME` or `new addendum DN`, and paste the additions in, in place of the template's text. Name a journal file by the date of one of its entries.
4. Text the branch added inside an existing decision, with no heading of its own, becomes an addendum: a file `DN-addendum-YYYY-MM-DD-name.md` headed `## Addendum to DN (date): what`. A decision number the branch holds that master does not have (D102, D103) is kept.
5. `python3 tools/project-log.py check --base origin/master`.
6. Compare the new files with the list from step 1, so nothing is dropped: the check cannot see an entry that was lost.

## Reading it as one

- `python3 tools/project-log.py journal`: the whole journal, newest first (the files in journal/, then JOURNAL.md).
- `python3 tools/project-log.py lessons`: every lesson, LESSONS.md first, then the files, oldest first.
- `python3 tools/project-log.py decisions`, or `... decisions D106 D107`: each decision followed by its addenda.
- `grep -r D106 project-log` finds a decision and every addendum, since the number is in the names and the headings.

The old files' entries are printed byte for byte. Nothing combined is committed: a generated file that every pull request changes would bring the conflicts back.

## The check "project-log"

CI's check "project-log" (`.github/workflows/project-log.yml`) runs `tools/project-log.py check` on every pull request, and on every push to master (there it looks at the files alone, so a number taken twice shows at once). It fails when the pull request:

- edits JOURNAL.md, LESSONS.md or DECISIONS.md (only the pointer at the top could be added, once, and it has been);
- edits or deletes a log file of an earlier pull request (one exception: when two pull requests took one number and both merged, the fix deletes one of the two files and adds it under the next free number, with the same name after the number);
- adds a file with a bad name or date, no SPDX header, text that is not UTF-8, a heading of the wrong form, or the template's WRITE-HERE left in;
- gives a new decision a number the base branch already has, or that another file has (it says the next free one: rename the file and its heading);
- adds an addendum to a decision that does not exist;
- adds **no journal file**. A trivial change (a typo, a version bump) may have the label `no-journal` instead.

## Rules of thumb

- Log the reason, not just the fact. "Chose X" is useless in six months; "Chose X because Y, at the cost of Z" is not.
- Record what was **not** verified as loudly as what was. Say "not run on a real board" when that is the case.
- Write it in the same pull request as the change. A decision made in chat and not logged is lost when the session ends.
- A decision is never rewritten: add an addendum, or a new decision that supersedes it.
- Plain language, no em dashes.
