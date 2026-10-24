<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-24, the project log in one file per pull request (D107)

- **Built:** `project-log/journal/`, `lessons/` and `decisions/`, one new file per pull request; a pointer at the top of JOURNAL.md, LESSONS.md and DECISIONS.md, which are history now and keep every old entry where it was; `tools/project-log.py` (the journal, the lessons and the decisions with their addenda printed as one; `new` and `next`; `check`); the check "project-log" in `.github/workflows/project-log.yml`; CLAUDE.md's logging rule and project-log/README.md. This entry, D107 and a lesson are the first files of the new kind.
- **For the open pull requests (#154, #155, #172):** when each next merges master, its additions to the three old files move into files of its own (D102 and D103 keep their numbers, which master does not have; #172's addendum to D106 becomes `decisions/D106-addendum-...`).
- **Not done:** the label `no-journal` and "project-log" as a required check are the owner's settings.
