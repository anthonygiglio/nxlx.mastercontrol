<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## A file that every change adds to is a conflict between every two changes (2026-10-24, D107)

Each pull request added its entry to the top of JOURNAL.md, so any two that were open at the same time conflicted there, and each merge cost the others a merge of master and a full run of CI; numbering decisions by "the next D" collided the same way. A shared running list is a lock nobody sees. Give each change a file of its own, named so that two cannot clash, build the combined view when someone reads it instead of committing it, and let a check catch the one thing names cannot (a number taken twice).

## A check that refuses a change must allow the fix it asks for (2026-10-24, D107)

The first check said "renumber one of the two files" when two merged pull requests had taken one number, and then refused that very renumbering, because deleting a file of an earlier pull request was refused. When a rule forbids something, walk through each of its own error messages and do what it says: the repair must pass. Also run the check where the damage lands (here, on every push to master), not only where it is meant to be prevented.
