<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Notes for claude sessions

Read first: [HANDOFF.md](HANDOFF.md), then [project-log/](project-log/README.md). The decisions, lessons and open items are there.

- Log as you go: a lasting decision goes in `project-log/DECISIONS.md`, a surprise in `LESSONS.md`, and add a dated entry to `JOURNAL.md` at the end of a session. Do it in the same pull request as the change.
- Write plain text with no em dashes (use commas, semicolons or new sentences).
- New code is Apache-2.0 with an SPDX header ("NXLX.Systems and contributors"). Never edit `LICENSE.md` or `AUTHORS.md`.
- One pull request per finished branch; short comment, merge when checks are green. Do not rewrite history.
- Ask before destructive or outward actions. Report outcomes faithfully, and never claim hardware behaviour that was not tested on hardware.
- Tests: `python3 -m unittest discover -s tests`; browser test `node tests/ui/panel.test.js`.

## Rules for agents (so a brief does not have to repeat them)

- Work in your own `git worktree` on a branch from `origin/master` (`git fetch` first). Never check out or edit in the shared main folder.
- Open the pull request after the first commit, then commit and push after every step. A session can be cut off at any moment; unpushed work is lost. Merge `origin/master` in when it moves; no rebase, no force-push. Do not merge your own pull request unless told to.
- Give every long command a timeout. To wait for checks use one blocking `gh pr checks <n> --watch --interval 30`, not a loop of short polls. Never wait on a socket or a thread without a deadline.
- Do not rerun a baseline: on the dev Mac (no mpv, no Playwright, BSD tools) these fail on master and are not yours: `tests.test_update` (10), `tests.test_install` (6), `tests.test_netd` (4), `tests.test_release` (3). Run only the test modules you touched, one at a time; CI (real mpv, Playwright) is the judge. Fix a red check at its cause; never loosen an assertion.
- Python is standard library only. Panel code is plain JavaScript with no build step, a strict CSP and `textContent` only. `node --check` what you touch.
- Scratch files go in a private folder from `mktemp -d`. Look at `git status` before each commit.
- Keep scratch files small and delete them when you finish: the dev Mac has little free disk, and a full disk stops every agent.
- Tests and probes use loopback only. Do not touch the test Pi, a projector or the network unless the brief says so.
- Anything that takes network input, reads devices, handles uploads or auth, or runs as root gets an independent read-only review before merging, and every finding gets a test.
- The panel's shared patterns (page shell, the real switch, state chips, inline confirm, list rows, apply on tap or Save) are in D42 and D43; a page holds its own controls by default, and a link to another page is fine where that is clearer (D51); pages for intense work need a laptop layout. Say server and client.
- Finish with a short report: what was built, what was left out, the pull request number, the state of every check, what was not tested, what you are unsure about.
