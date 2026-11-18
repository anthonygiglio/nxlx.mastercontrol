<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-18, J-156: the strobe says before the press that nothing plays

- GET /api/strobe took "available" from the module and the bundled file only, so with no picture the button was lit and the hold was refused after the press (409, "Nothing with a picture is playing"). `Strobe.state` now takes available and why from `effects.available()`, the check a put of any effect already makes (module on, a picture playing and one the filter can read), so there is no second check.
- The panel already disabled the button and showed `why` when `available` is false, so web/app.js is unchanged.
- The hold still refuses with no picture (it was never changed). Tests in tests/test_strobe.py: the state follows the picture (true with one, false with the reason without); the existing hold refusal test stays. tests.test_strobe and tests.test_effects pass in WSL. The Playwright strobe test already waits for a picture before it expects the button enabled; it was not run here.
- Not proven: the state is read once when the card is drawn, so a clip that starts or stops after that is not shown until the card is drawn again.
