<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Playwright 1.56 cannot install its browser's libraries on Ubuntu 26.04 (2026-10-20)

(2026-10-20, the HP's WSL.) `npx playwright install-deps` stops with "Cannot install dependencies for ubuntu26.04-x64". `ldd` on the headless shell named only three missing files, all in two packages of the distribution: `sudo apt-get install libnspr4 libnss3`. Ask `ldd` before reaching for the installer.
