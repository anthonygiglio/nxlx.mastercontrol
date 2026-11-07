<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## A child process's stdout is not only yours (2026-10-20)

(2026-10-20, the design sandbox, D103.) The switcher read the box's first stdout line as its "ready" message; the panel prints some log lines to stdout ("guest controls locked by ..."), so the scenario that locks guest controls never started. The box now keeps the real stdout for its one line and points `sys.stdout` at stderr. Read a handshake from a channel nothing else writes to.

## A Python process stopped with SIGTERM runs no `finally` (2026-10-20)

(2026-10-20, D103.) Stopping the sandbox with `pkill` left the box's temporary folder (and its mpv for a moment) behind; Ctrl+C did not. Both the switcher and the box now turn SIGTERM into a normal exit, and a test sends SIGTERM and looks for what is left. A test that the break-check made fail: without the handler it finds the folder and the log.

## Never kill by a pattern on a shared machine (2026-10-20)

(2026-10-20, while fixing the review of #155.) A debug script of mine killed "every process whose command line names the box's folder" with the folder in a variable. The sandbox had not started, the variable was empty, `grep -l "" /proc/*/cmdline` matches every process, and most processes of the WSL user were SIGKILLed: the owner's running sandbox, a CI watch, and whatever other sessions had running there. They were told. Kill only a PID you started or read and checked; `set -u` and a test that a variable is not empty before any destructive line; never `pkill -f` a short pattern (one matched its own shell the same day). The sandbox's own sweep (`serve.leftovers`) only runs for a folder that is a `nxlx-sandbox-*` folder of the temp folder.

## A process started in the background by a shell script has SIGINT ignored, and Python keeps it ignored (2026-10-20)

A debug run that sent SIGINT to the switcher's group seemed to show the fix not working; the switcher had never seen the signal. A program that must stop on Ctrl+C sets its own SIGINT handler.
