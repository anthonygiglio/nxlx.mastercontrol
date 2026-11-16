#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Build a release bundle for nxlx-update: dist/pvj-<version>.tar.gz, its .sha256
# and (with --key) an OpenSSH signature dist/pvj-<version>.tar.gz.sig.
#
#   tools/make-release.sh 4.0.1 [--key ~/.ssh/pvj-release] [--allow-dirty]
#
# The version must match mctl/__init__.py. The archive is reproducible: the same
# commit always gives the same bytes, so anyone can check a release.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
version="${1:-}"
key=""
dirty_ok=0
[ $# -ge 1 ] && shift
while [ $# -gt 0 ]; do
	case "$1" in
	--key) key="${2:?--key needs a file}"; shift 2 ;;
	--allow-dirty) dirty_ok=1; shift ;;
	*) echo "unknown option $1" >&2; exit 2 ;;
	esac
done

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 N.N.N [--key FILE]" >&2; exit 2; }
# The package folder is mctl. A bundle of a release from before the rename is built with that checkout's own copy of
# this script (it works on the checkout it lives in).
pkg=mctl
declared="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$pkg/__init__.py")"
[ "$declared" = "$version" ] || { echo "$pkg/__init__.py says $declared, not $version; update it first" >&2; exit 1; }

# The bundle is built from files in the working tree; refuse if they differ from the commit, so a
# release always matches a commit anyone can check out.
if [ "$dirty_ok" = 0 ] && ! git diff --quiet HEAD -- "$pkg" bin install render; then
	echo "$pkg/, bin/, install/ or render/ have uncommitted changes; commit them (or pass --allow-dirty for a test build)" >&2
	exit 1
fi

mkdir -p dist
out="dist/pvj-$version.tar.gz"
files="$(git ls-files "$pkg" bin install render | sort)"      # render/ is source only (D95): it is built on the box
[ -n "$files" ] || { echo "no tracked files found (run inside the git checkout)" >&2; exit 1; }
# No stubs for the updater of the releases before the rename (PR 6 had them, for one release): from the rename of the
# system names (PR 7) on, that updater must refuse this bundle, and does, because it has no bin/pvj-player. Its
# installer would have to rename the node under a running update; the first install of this release on such a node is
# install.sh over ssh, which converts the node (bin/nxlx-convert).
# shellcheck disable=SC2086
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner --transform "s,^,pvj-$version/," \
	-cf - $files | gzip -n -9 > "$out"
sum="$(sha256sum "$out" | cut -d' ' -f1)"
printf '%s  %s\n' "$sum" "$(basename "$out")" > "$out.sha256"
echo "built $out"
echo "sha256 $sum"

if [ -n "$key" ]; then
	rm -f "$out.sig"
	ssh-keygen -Y sign -f "$key" -n pvj-release "$out" >/dev/null
	echo "signed $out.sig"
else
	echo "not signed (pass --key to sign); nxlx-update refuses unsigned bundles" >&2
fi
