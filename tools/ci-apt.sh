#!/bin/sh
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
#
# Install packages on a CI runner, giving up on a slow mirror early and trying again.
#
#   tools/ci-apt.sh mpv xvfb ...
#
# Why: between 2026-10-05 and 2026-10-08 the "Install mpv" steps ran out of their ten minutes about ten times,
# always before a test had run: the runner's Ubuntu mirror answered at a crawl or not at all. A whole attempt
# normally takes under a minute, so each attempt gets 90 seconds for the lists and 90 for the packages and there are three of them; apt is also told
# to give up on a silent connection after 20 seconds and to retry a failed download itself. Three attempts fit
# inside the step's own limit, and a step that still fails says so with what it printed.
set -u
[ "$#" -gt 0 ] || { echo "usage: $0 package..." >&2; exit 64; }
ATTEMPTS=${CI_APT_ATTEMPTS:-3}
EACH=${CI_APT_SECONDS:-90}
OPTS="-o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 -o DPkg::Lock::Timeout=60"
n=1
while :; do
    echo "ci-apt: attempt $n of $ATTEMPTS: $*"
    # shellcheck disable=SC2086  # OPTS is a list of words on purpose
    if sudo timeout -k 10 "$EACH" apt-get $OPTS update &&
       sudo timeout -k 10 "$EACH" env DEBIAN_FRONTEND=noninteractive apt-get $OPTS install -y --no-install-recommends "$@"; then
        echo "ci-apt: installed on attempt $n"
        exit 0
    fi
    [ "$n" -lt "$ATTEMPTS" ] || { echo "ci-apt: gave up after $ATTEMPTS attempts" >&2; exit 1; }
    n=$((n + 1))
    sleep 5
done
