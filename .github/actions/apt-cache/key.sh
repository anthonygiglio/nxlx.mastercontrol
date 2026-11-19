#!/bin/bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# The cache key of a job's apt packages: $1 = the package names. Writes base, hash and run to $GITHUB_OUTPUT.
set -eu
# shellcheck disable=SC2086  # $1 is a list of package names, split into words on purpose
hash="$(printf '%s\n' $1 | sort -u | sha256sum | cut -c1-16)"
{
  echo "hash=apt-${RUNNER_OS}-${hash}"
  echo "base=apt-${RUNNER_OS}-${hash}-${ImageOS:-unknown}-${ImageVersion:-unknown}"
} >> "$GITHUB_OUTPUT"
