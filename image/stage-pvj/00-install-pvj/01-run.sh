#!/bin/bash -e
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs on the build host. files/pvj-src is filled in by the workflow with the
# pvj/, bin/ and install/ folders of this repository.
[ -f files/pvj-src/install/install.sh ] || { echo "files/pvj-src is missing; run through image.yml" >&2; exit 1; }

install -d "${ROOTFS_DIR}/tmp/pvj-src"
cp -a files/pvj-src/. "${ROOTFS_DIR}/tmp/pvj-src/"

on_chroot << 'CHROOT'
/tmp/pvj-src/install/install.sh --offline --no-start
systemctl enable pvj-player.service pvj-web.service pvj-netd.service pvj-sysd.service pvj-supportd.service
CHROOT

rm -rf "${ROOTFS_DIR}/tmp/pvj-src"
