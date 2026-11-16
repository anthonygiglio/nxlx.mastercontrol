#!/bin/bash -e
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs on the build host. files/nxlx-src is filled in by the workflow with the
# mctl/, bin/ and install/ folders of this repository.
[ -f files/nxlx-src/install/install.sh ] || { echo "files/nxlx-src is missing; run through image.yml" >&2; exit 1; }

install -d "${ROOTFS_DIR}/tmp/nxlx-src"
cp -a files/nxlx-src/. "${ROOTFS_DIR}/tmp/nxlx-src/"

on_chroot << 'CHROOT'
/tmp/nxlx-src/install/install.sh --offline --no-start
systemctl enable nxlx-player.service nxlx-web.service nxlx-netd.service nxlx-sysd.service nxlx-supportd.service nxlx-sound.service nxlx-link.service nxlx-raster.socket
CHROOT

rm -rf "${ROOTFS_DIR}/tmp/nxlx-src"
