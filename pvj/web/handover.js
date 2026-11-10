// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The page POST /handover answers with when a device was signed in from another node (D132): the cookie is set, and the
// move to the panel is made here because a redirect after a cross-site POST would not carry a SameSite=Strict cookie.
location.replace('/');
