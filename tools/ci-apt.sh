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
#
# Cache of the downloaded .deb files (J-173). On 2026-11-18 the mirror was slow all afternoon and jobs failed with
# "gave up after 3 attempts" before any test. When CI_APT_CACHE names a folder (the workflow's apt-cache action
# restores it before the job and saves it after), the .deb files in it are copied into apt's own cache first, so
# apt-get install has nothing to download for them, and after a successful install apt's cache is copied back.
# apt still runs update, and it checks every file in its cache against the lists it has just read (version, size,
# checksum): a deb of another version or a damaged one is ignored and fetched again, so a stale or wrong cache
# costs a download and cannot install the wrong package. With no CI_APT_CACHE, or an empty folder, nothing changes.
set -u
[ "$#" -gt 0 ] || { echo "usage: $0 package..." >&2; exit 64; }
ATTEMPTS=${CI_APT_ATTEMPTS:-3}
EACH=${CI_APT_SECONDS:-90}
OPTS="-o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 -o DPkg::Lock::Timeout=60"
# After a failed attempt, ask Ubuntu's own archive instead of the runner's cloud mirror. On 2026-10-08 one runner
# failed all three attempts on azure.archive.ubuntu.com (the lists came at once, the packages at a crawl), so
# trying the same mirror again was no cure. What an attempt had already downloaded stays in apt's cache, so the
# next attempt carries on from there. Only the files apt reads its mirrors from are touched, and only on a runner.
other_mirror() {
    for f in /etc/apt/apt-mirrors.txt /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
        [ -f "$f" ] || continue
        if grep -q 'azure\.archive\.ubuntu\.com' "$f"; then
            echo "ci-apt: $f: azure.archive.ubuntu.com replaced by archive.ubuntu.com"
            sudo sed -i 's|//azure\.archive\.ubuntu\.com/|//archive.ubuntu.com/|g' "$f"
        fi
    done
}
# An attempt timed out in the middle of unpacking leaves dpkg marked as interrupted, and every later attempt then stops
# at once with "dpkg was interrupted" (2026-11-01 and 2026-11-02, two PRs). Finish what it began first. A failure here
# is the next attempt's to report. 20 seconds, so that all of it still fits inside the step's ten minutes.
finish_dpkg() {
    sudo timeout -k 5 20 env DEBIAN_FRONTEND=noninteractive dpkg --configure -a || echo "ci-apt: dpkg --configure -a did not finish"
}
# Cache steps never fail the install: a problem here costs a download, not the job.
ARCHIVES=${CI_APT_ARCHIVES:-/var/cache/apt/archives}
CACHE=${CI_APT_CACHE:-}
cache_restore() {
    [ -n "$CACHE" ] || return 0
    if ls "$CACHE"/*.deb >/dev/null 2>&1; then
        echo "ci-apt: cache: $(find "$CACHE" -maxdepth 1 -name "*.deb" | wc -l) packages in $CACHE"
        sudo cp -u "$CACHE"/*.deb "$ARCHIVES"/ || echo "ci-apt: cache: restore failed, carrying on"
    else
        echo "ci-apt: cache: $CACHE is empty (cold)"
    fi
}
cache_save() {
    [ -n "$CACHE" ] || return 0
    # autoclean drops the files whose version the lists no longer offer, so old versions do not pile up.
    sudo apt-get autoclean >/dev/null 2>&1 || true
    mkdir -p "$CACHE" || return 0
    if ls "$ARCHIVES"/*.deb >/dev/null 2>&1; then
        find "$CACHE" -maxdepth 1 -name '*.deb' -delete
        sudo cp "$ARCHIVES"/*.deb "$CACHE"/ || echo "ci-apt: cache: save failed"
        sudo chown -R "$(id -u):$(id -g)" "$CACHE" || true
        echo "ci-apt: cache: $(find "$CACHE" -maxdepth 1 -name "*.deb" | wc -l) packages kept in $CACHE"
    fi
}
cache_restore
n=1
while :; do
    echo "ci-apt: attempt $n of $ATTEMPTS: $*"
    # shellcheck disable=SC2086  # OPTS is a list of words on purpose
    if sudo timeout -k 10 "$EACH" apt-get $OPTS update &&
       sudo timeout -k 10 "$EACH" env DEBIAN_FRONTEND=noninteractive apt-get $OPTS install -y --no-install-recommends "$@"; then
        echo "ci-apt: installed on attempt $n"
        cache_save
        exit 0
    fi
    [ "$n" -lt "$ATTEMPTS" ] || { echo "ci-apt: gave up after $ATTEMPTS attempts" >&2; exit 1; }
    n=$((n + 1))
    finish_dpkg
    other_mirror
    sleep 5
done
